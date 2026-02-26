"""
Tests for CameraSource JPEG frame parsing logic.
Validates that the chunk-based MJPEG parser correctly extracts JPEG frames
from a continuous byte stream (as produced by rpicam-vid).
"""
import io
import threading
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Minimal mock of SharedState for testing
class MockLock:
    def __enter__(self): return self
    def __exit__(self, *a): pass

class MockState:
    def __init__(self):
        self.lock = MockLock()
        self.camera_ok = False
    def add_log(self, *a): pass


def make_jpeg(payload: bytes) -> bytes:
    """Build a minimal JPEG: SOI + payload + EOI."""
    return b'\xff\xd8' + payload + b'\xff\xd9'


def test_single_frame():
    """A single complete JPEG frame is extracted."""
    from pi_rover_system import CameraSource
    state = MockState()
    cam = CameraSource(state)

    frame = make_jpeg(b'\x00\x01\x02\x03')
    # Simulate a process with stdout that yields the frame then EOF
    cam.process = type('P', (), {
        'poll': lambda self: None,
        'stdout': io.BufferedReader(io.BytesIO(frame)),
    })()

    # Run one iteration in a thread, then stop
    cam.running = True
    t = threading.Thread(target=cam.run, daemon=True)
    t.start()
    time.sleep(0.3)
    cam.running = False
    t.join(timeout=2)

    result = cam.get_jpeg()
    assert result == frame, f"Expected {frame!r}, got {result!r}"
    print("PASS: test_single_frame")


def test_multiple_frames_keeps_latest():
    """When multiple frames are buffered, only the latest is kept."""
    from pi_rover_system import CameraSource
    state = MockState()
    cam = CameraSource(state)

    frame1 = make_jpeg(b'\xAA\xBB')
    frame2 = make_jpeg(b'\xCC\xDD')
    frame3 = make_jpeg(b'\xEE\xFF')
    stream = frame1 + frame2 + frame3

    cam.process = type('P', (), {
        'poll': lambda self: None,
        'stdout': io.BufferedReader(io.BytesIO(stream)),
    })()

    cam.running = True
    t = threading.Thread(target=cam.run, daemon=True)
    t.start()
    time.sleep(0.3)
    cam.running = False
    t.join(timeout=2)

    result = cam.get_jpeg()
    assert result == frame3, f"Expected latest frame {frame3!r}, got {result!r}"
    print("PASS: test_multiple_frames_keeps_latest")


def test_garbage_before_soi():
    """Garbage bytes before the SOI marker are skipped."""
    from pi_rover_system import CameraSource
    state = MockState()
    cam = CameraSource(state)

    garbage = b'\x00\x11\x22\x33\x44'
    frame = make_jpeg(b'\xAA\xBB\xCC')
    stream = garbage + frame

    cam.process = type('P', (), {
        'poll': lambda self: None,
        'stdout': io.BufferedReader(io.BytesIO(stream)),
    })()

    cam.running = True
    t = threading.Thread(target=cam.run, daemon=True)
    t.start()
    time.sleep(0.3)
    cam.running = False
    t.join(timeout=2)

    result = cam.get_jpeg()
    assert result == frame, f"Expected {frame!r}, got {result!r}"
    print("PASS: test_garbage_before_soi")


def test_partial_frame_waits():
    """A partial frame (SOI but no EOI) does not produce output."""
    from pi_rover_system import CameraSource
    state = MockState()
    cam = CameraSource(state)

    # Stream with SOI but no EOI
    partial = b'\xff\xd8\x01\x02\x03'

    cam.process = type('P', (), {
        'poll': lambda self: None,
        'stdout': io.BufferedReader(io.BytesIO(partial)),
    })()

    cam.running = True
    t = threading.Thread(target=cam.run, daemon=True)
    t.start()
    time.sleep(0.3)
    cam.running = False
    t.join(timeout=2)

    result = cam.get_jpeg()
    assert result is None, f"Expected None for partial frame, got {result!r}"
    print("PASS: test_partial_frame_waits")


def test_frame_with_embedded_ff():
    """JPEG frames with embedded 0xFF bytes are handled correctly."""
    from pi_rover_system import CameraSource
    state = MockState()
    cam = CameraSource(state)

    # Payload contains 0xFF bytes (but not 0xFF 0xD9)
    payload = b'\xff\x00\xff\x00\xff\xda\x01\x02'
    frame = make_jpeg(payload)

    cam.process = type('P', (), {
        'poll': lambda self: None,
        'stdout': io.BufferedReader(io.BytesIO(frame)),
    })()

    cam.running = True
    t = threading.Thread(target=cam.run, daemon=True)
    t.start()
    time.sleep(0.3)
    cam.running = False
    t.join(timeout=2)

    result = cam.get_jpeg()
    assert result == frame, f"Expected {frame!r}, got {result!r}"
    print("PASS: test_frame_with_embedded_ff")


def test_large_frame():
    """A large JPEG frame (simulating real camera output) is handled."""
    from pi_rover_system import CameraSource
    state = MockState()
    cam = CameraSource(state)

    # Simulate a 200KB frame (typical MJPEG frame size)
    payload = bytes(range(256)) * 800  # 204800 bytes
    frame = make_jpeg(payload)

    cam.process = type('P', (), {
        'poll': lambda self: None,
        'stdout': io.BufferedReader(io.BytesIO(frame)),
    })()

    cam.running = True
    t = threading.Thread(target=cam.run, daemon=True)
    t.start()
    time.sleep(0.5)
    cam.running = False
    t.join(timeout=2)

    result = cam.get_jpeg()
    assert result == frame, f"Frame mismatch: expected len={len(frame)}, got len={len(result) if result else 'None'}"
    print("PASS: test_large_frame")


if __name__ == "__main__":
    test_single_frame()
    test_multiple_frames_keeps_latest()
    test_garbage_before_soi()
    test_partial_frame_waits()
    test_frame_with_embedded_ff()
    test_large_frame()
    print("\nAll camera parsing tests passed!")
