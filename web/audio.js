// All sound is synthesized locally. No samples or photo data leave the browser.
export function synthesizeNote(context, destination, note, instrument, at, duration) {
  const frequency = 440 * 2 ** ((note.midi - 69) / 12);
  const oscillator = context.createOscillator();
  const envelope = context.createGain();
  const filter = context.createBiquadFilter();
  const piano = instrument === "piano";
  const cello = instrument === "cello";
  const real = new Float32Array(24);
  const imag = new Float32Array(24);
  for (let harmonic = 1; harmonic < imag.length; harmonic++) {
    imag[harmonic] = piano
      ? 1 / harmonic ** 2.1
      : (harmonic % 2 ? 1 : 0.65) / harmonic ** (cello ? 1.15 : 0.85);
  }
  oscillator.setPeriodicWave(context.createPeriodicWave(real, imag));
  oscillator.frequency.value = frequency;
  filter.type = "lowpass";
  filter.frequency.value = Math.min(14000, piano ? frequency * 9 + 1200 : cello ? 3200 : 6500);
  filter.Q.value = 0.3;
  const held = Math.max(0.035, duration * note.gate);
  const attack = Math.min(piano ? 0.008 : cello ? 0.09 : 0.055, held * 0.3);
  const release = piano ? 0.16 : 0.11;
  const amplitude = 0.19 * note.velocity;
  envelope.gain.setValueAtTime(0.0001, at);
  envelope.gain.exponentialRampToValueAtTime(Math.max(0.001, amplitude), at + attack);
  if (piano) {
    envelope.gain.exponentialRampToValueAtTime(Math.max(0.0002, amplitude * 0.14), at + held);
  } else {
    envelope.gain.setValueAtTime(amplitude * 0.85, at + held);
  }
  envelope.gain.exponentialRampToValueAtTime(0.0001, at + held + release);
  oscillator.connect(filter).connect(envelope).connect(destination);
  let vibrato;
  let vibratoDepth;
  if (!piano) {
    vibrato = context.createOscillator();
    vibratoDepth = context.createGain();
    vibrato.frequency.value = cello ? 4.8 : 5.6;
    vibratoDepth.gain.setValueAtTime(0, at);
    vibratoDepth.gain.linearRampToValueAtTime(frequency * 0.003, at + Math.min(0.22, held));
    vibrato.connect(vibratoDepth).connect(oscillator.frequency);
    vibrato.start(at);
    vibrato.stop(at + held + release);
  }
  oscillator.start(at);
  oscillator.stop(at + held + release);
  oscillator.onended = () => {
    oscillator.disconnect();
    filter.disconnect();
    envelope.disconnect();
    vibrato?.disconnect();
    vibratoDepth?.disconnect();
  };
  return {
    stop() {
      try {
        oscillator.stop();
        vibrato?.stop();
      } catch (error) {
        if (error.name !== "InvalidStateError") throw error;
      }
    },
  };
}

export class ScorePlayer {
  constructor(onUpdate, onError) {
    this.onUpdate = onUpdate;
    this.onError = onError;
    this.instrument = "piano";
    this.speed = 1;
    this.volume = 0.7;
    this.position = 0;
    this.playing = false;
    this.generation = 0;
    this.voices = new Set();
  }

  load(score) {
    this.stop();
    this.score = score;
    this.onUpdate(this);
  }

  async play() {
    const generation = ++this.generation;
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) throw new Error("Audio playback is not supported in this browser. Try a current Safari, Chrome or Firefox.");
    if (!this.context) {
      this.context = new AudioContext();
      this.master = this.context.createGain();
      const compressor = this.context.createDynamicsCompressor();
      compressor.threshold.value = -12;
      compressor.ratio.value = 8;
      this.master.connect(compressor).connect(this.context.destination);
      this.master.gain.value = this.volume;
      this.context.onstatechange = () => {
        if (this.playing && this.context.state !== "running") this.pause();
      };
    }
    await this.context.resume();
    if (generation !== this.generation) return;
    if (this.context.state !== "running") throw new Error("Your browser blocked audio. Check device sound settings, then tap Play again.");
    if (!this.score || this.playing) return;
    if (this.position >= this.score.duration - 0.01) this.position = 0;
    this.startPosition = this.position;
    this.startedAt = this.context.currentTime + 0.06;
    this.nextIndex = this.score.events.findIndex(note => note.time + note.duration > this.position + 0.001);
    if (this.nextIndex < 0) this.nextIndex = this.score.events.length;
    this.playing = true;
    this.timer = setInterval(() => this.tick(), 30);
    this.tick();
  }

  tick() {
    if (!this.playing) return;
    this.position = Math.min(this.score.duration, this.startPosition + Math.max(0, this.context.currentTime - this.startedAt) * this.speed);
    const horizon = this.position + 0.18 * this.speed;
    try {
      while (this.nextIndex < this.score.events.length) {
        const note = this.score.events[this.nextIndex];
        if (note.time > horizon) break;
        this.nextIndex++;
        if (note.time + note.duration <= this.position) continue;
        const start = Math.max(note.time, this.startPosition);
        const at = Math.max(this.context.currentTime, this.startedAt + (start - this.startPosition) / this.speed);
        const duration = (note.time + note.duration - start) / this.speed;
        const voice = synthesizeNote(this.context, this.master, note, this.instrument, at, duration);
        this.voices.add(voice);
        setTimeout(() => this.voices.delete(voice), Math.max(0, (at - this.context.currentTime + duration + 0.3) * 1000));
      }
    } catch (error) {
      this.pause();
      this.onError(`Audio could not be played: ${error.message}`);
      return;
    }
    this.onUpdate(this);
    if (this.position >= this.score.duration) {
      clearInterval(this.timer);
      this.playing = false;
      this.onUpdate(this);
    }
  }

  pause() {
    this.generation++;
    if (this.playing) {
      this.position = Math.min(this.score.duration, this.startPosition + Math.max(0, this.context.currentTime - this.startedAt) * this.speed);
    }
    clearInterval(this.timer);
    this.playing = false;
    for (const voice of this.voices) voice.stop();
    this.voices.clear();
    this.onUpdate(this);
  }

  stop() {
    this.pause();
    this.position = 0;
    this.onUpdate(this);
  }

  async seek(position) {
    const wasPlaying = this.playing;
    this.pause();
    this.position = Math.max(0, Math.min(this.score?.duration || 0, position));
    this.onUpdate(this);
    if (wasPlaying) await this.play();
  }

  async setSpeed(value) {
    const wasPlaying = this.playing;
    this.pause();
    this.speed = value;
    if (wasPlaying) await this.play();
  }

  setVolume(value) {
    this.volume = value;
    if (this.master) this.master.gain.setTargetAtTime(value, this.context.currentTime, 0.02);
  }
}
