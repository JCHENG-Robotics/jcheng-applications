"""Shared execution core for the linear and tree drivers.

The core is transport-neutral and ROS-free. It owns the preflight checks,
command construction, the single submission per step, the terminal-result
classification and the stop-once policy. Both the linear ``TaskRunner`` and the
M1.5 tree probe call the same functions, so the two drivers cannot drift.
"""
