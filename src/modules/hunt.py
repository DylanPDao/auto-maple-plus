"""
Hunt movement: instead of looping through a routine's Points in order, picks the next Point to
visit based on where monsters have recently been found and how long since each was visited.
"""

import math
import time
import random
from src.common import utils
from src.routine.components import Point


# A Point's "heat" (monsters fought there) halves every HEAT_HALF_LIFE seconds
HEAT_HALF_LIFE = 120

# How long (s) since a visit before a Point counts as fully due again
FRESHNESS_TIME = 120

# Score weights: monster heat, time since the last visit, distance (minimap units) and randomness.
# Tuned in simulation so busy Points get ~2.4x the visits of quiet ones while every Point still
# gets at least ~9% and the order never settles into a fixed cycle
HEAT_WEIGHT = 1.5
FRESHNESS_WEIGHT = 1.5
DISTANCE_WEIGHT = 0.5
RANDOMNESS = 0.7


class HuntPlanner:
    def __init__(self):
        self.reset()

    def reset(self):
        """Forgets all visits and heat, e.g. when a new routine is loaded."""

        self.start = time.time()
        self.visits = {}            # Point index -> time of the last visit
        self.heat = {}              # Point index -> (heat, time it was last updated)

    def record(self, index, kills):
        """Records a visit to the Point at INDEX where KILLS monsters were fought."""

        now = time.time()
        self.visits[index] = now
        self.heat[index] = (self._heat(index, now) + kills, now)

    def next_index(self, routine, current):
        """
        Picks the index of the next Point to visit after the one at CURRENT.
        :return:    The chosen index, or the next index in order if there are fewer than two Points.
        """

        points = [i for i, c in enumerate(routine.sequence) if isinstance(c, Point)]
        if len(points) < 2 or current not in points:
            return (current + 1) % len(routine.sequence)
        now = time.time()
        here = routine.sequence[current].location
        best, best_score = None, -math.inf
        for i in points:
            if i == current:
                continue
            score = self.score(i, now, utils.distance(here, routine.sequence[i].location))
            if score > best_score:
                best, best_score = i, score
        return best

    def score(self, index, now, distance):
        heat = self._heat(index, now)
        # Points never visited count as visited just before hunting started, so they're due soon
        since = now - self.visits.get(index, self.start - FRESHNESS_TIME)
        return (HEAT_WEIGHT * heat / (1 + heat)
                + FRESHNESS_WEIGHT * min(since, FRESHNESS_TIME) / FRESHNESS_TIME
                - DISTANCE_WEIGHT * distance
                + RANDOMNESS * random.random())

    def _heat(self, index, now):
        heat, updated = self.heat.get(index, (0, now))
        return heat * 0.5 ** ((now - updated) / HEAT_HALF_LIFE)
