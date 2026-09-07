"""Grid-based crowd flow simulation and shortest-path routing."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class FlowCell:
    row: int
    col: int
    density: float = 0.0
    capacity: float = 6.0
    exits: list[tuple[int, int]] = field(default_factory=list)

    @property
    def overload(self) -> float:
        return max(0.0, self.density - self.capacity)


class FlowSimulator:
    """Minimal digital-twin style grid flow simulator.

    Features:
      - grid occupancy tracking per cell
      - BFS shortest-path calculation between any two cells
      - optional stochastic flow updates to animate crowd movement
    """

    def __init__(
        self,
        grid_size: tuple[int, int] = (4, 6),
        default_capacity: float = 6.0,
        seed: Optional[int] = None,
    ) -> None:
        self.rows, self.cols = grid_size
        self.default_capacity = default_capacity
        self._rng = random.Random(seed)

        self.grid: list[list[FlowCell]] = [
            [
                FlowCell(row=r, col=c, capacity=default_capacity)
                for c in range(self.cols)
            ]
            for r in range(self.rows)
        ]

    # ------------------------------------------------------------------
    # Grid helpers
    # ------------------------------------------------------------------
    def _in_bounds(self, r: int, c: int) -> bool:
        return 0 <= r < self.rows and 0 <= c < self.cols

    def set_density(self, row: int, col: int, density: float) -> None:
        if not self._in_bounds(row, col):
            return
        self.grid[row][col].density = max(0.0, float(density))

    def get_cell(self, row: int, col: int) -> Optional[FlowCell]:
        if not self._in_bounds(row, col):
            return None
        return self.grid[row][col]

    def density_grid(self) -> np.ndarray:
        return np.array(
            [[self.grid[r][c].density for c in range(self.cols)] for r in range(self.rows)],
            dtype=float,
        )

    # ------------------------------------------------------------------
    # Shortest path (4-direction, uniform cost, passable when not overloaded)
    # ------------------------------------------------------------------
    def calculate_shortest_paths(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> list[tuple[int, int]]:
        """BFS shortest path from ``start`` to ``end`` on the grid.

        Returns a list of (row, col) positions including both endpoints.
        Returns an empty list when no path is reachable.
        """
        sr, sc = start
        er, ec = end
        if not (self._in_bounds(sr, sc) and self._in_bounds(er, ec)):
            return []

        if start == end:
            return [start]

        distances = {start: 0.0}
        parents: dict[tuple[int, int], tuple[int, int]] = {}
        queue = [(0.0, start)]
        visited = set()

        while queue:
            queue.sort(key=lambda x: x[0])
            dist, cur = queue.pop(0)
            if cur in visited:
                continue
            visited.add(cur)

            if cur == end:
                break

            r, c = cur
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = r + dr, c + dc
                nxt = (nr, nc)
                if not self._in_bounds(nr, nc):
                    continue
                cell = self.grid[nr][nc]
                if cell.overload > 0 and nxt != end:
                    continue

                cost = 1.0 + cell.density
                new_dist = dist + cost

                if new_dist < distances.get(nxt, float('inf')):
                    distances[nxt] = new_dist
                    parents[nxt] = cur
                    queue.append((new_dist, nxt))

        if end not in parents:
            return []

        path: list[tuple[int, int]] = [end]
        while path[-1] != start:
            path.append(parents[path[-1]])
        path.reverse()
        return path

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------
    def step(self, temperature: float = 0.2) -> np.ndarray:
        """Perform one stochastic flow diffusion step and return the new density grid."""
        new_grid = self.density_grid()
        for r in range(self.rows):
            for c in range(self.cols):
                delta = self._rng.uniform(-temperature, temperature)
                new_grid[r, c] = max(0.0, new_grid[r, c] + delta)
        for r in range(self.rows):
            for c in range(self.cols):
                self.grid[r][c].density = float(new_grid[r, c])
        return new_grid
