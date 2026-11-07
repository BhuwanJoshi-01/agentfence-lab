"""Built-in fixture generators. Importing this package registers them.

Generators produce inert, synthetic toy projects. They never fetch anything, never
include real credentials or live endpoints, and never ship executable setup steps.
"""

from agentfence.scenarios.generators import calculator, inventory, receipt, toolcli

__all__ = ["calculator", "inventory", "receipt", "toolcli"]
