from src.detector import Detection
from src.occupancy import OccupancyGrid

def test_maps_detections_to_deterministic_4x6_cells():
    result = OccupancyGrid().map([Detection((0,0,1,1), (0, 0), .9), Detection((0,0,1,1), (639, 479), .9)], (480,640,3))
    assert len(result.grid) == 4 and len(result.grid[0]) == 6
    assert result.grid[0][0] == 1 and result.grid[3][5] == 1
