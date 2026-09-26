"""Point the app at a throwaway data folder before anything imports track_anatomy.config."""
import os
import tempfile

os.environ.setdefault("TRACK_ANATOMY_DATA", tempfile.mkdtemp(prefix="track-anatomy-test-"))
