"""Monotonic inactivity clock for an open conversation, independent of audio upload."""


class SilenceTimer:
    def __init__(self, seconds=20):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 1 <= seconds <= 600:
            seconds = 20
        self.seconds = float(seconds)
        self.since = None

    def reset(self, now=None):
        self.since = now

    def observe(self, now, *, connected, waiting, speech=False):
        # Recognition, model/tool work, playback and held push-to-talk are not user silence.
        if not connected or not waiting:
            self.since = None
            return False
        if speech or self.since is None:
            self.since = now
        return now - self.since >= self.seconds
