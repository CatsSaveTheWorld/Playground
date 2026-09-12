"""Compatibility shim: Sequence execution was merged into AutomationExecutor."""
from ...scheduler.executor import AutomationExecutor


SequenceExecutor = AutomationExecutor
