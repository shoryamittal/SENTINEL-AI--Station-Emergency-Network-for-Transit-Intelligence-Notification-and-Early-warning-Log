import numpy as np

from src.camera import FrameSource
from src.contracts import SourceMode


def test_latest_frame_is_none_before_first_read():
    source = FrameSource(SourceMode.SIMULATION)
    assert source.get_latest_frame() is None


def test_simulation_frame_is_available_and_defensive():
    original = np.zeros((4, 5, 3), dtype=np.uint8)
    original[0, 0] = (1, 2, 3)
    source = FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: original)
    source.read()
    frame = source.get_latest_frame()
    assert frame is not None
    frame[0, 0] = (9, 9, 9)
    assert source.get_latest_frame()[0, 0].tolist() == [1, 2, 3]


def test_camera_feed_route_is_local_multipart(monkeypatch):
    import deploy

    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(deploy.runtime.source, "get_latest_frame", lambda: frame.copy())
    response = deploy.app.test_client().get("/camera-feed", buffered=False)
    assert response.status_code == 200
    assert response.mimetype == "multipart/x-mixed-replace"
    assert response.content_type.startswith("multipart/x-mixed-replace; boundary=frame")
    first_chunk = next(response.response)
    response.close()
    assert first_chunk.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
