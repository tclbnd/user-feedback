"""Prepared statements and their plan cache.

Planning is expensive for the API's hot queries, so a prepared statement
plans once and keeps the plan for every later execution.
"""
from .executor import run_plan
from .planner import plan


class PreparedStatement:
    def __init__(self, server, name, query):
        self.server = server
        self.name = name
        self.query = query
        self.plan = None
        self.executions = 0
        self.plannings = 0

    def execute(self):
        if self.plan is None:
            self.plan = plan(self.query, self.server.catalog)
            self.plannings += 1
            self.server.plannings += 1
        self.executions += 1
        cost = run_plan(self.plan, self.query, self.server.catalog)
        return {"plan": self.plan.describe(), "cost": cost}


class Server:
    def __init__(self, catalog):
        self.catalog = catalog
        self.statements = {}
        self.plannings = 0

    def prepare(self, name, query):
        self.statements[name] = PreparedStatement(self, name, query)

    def execute(self, name):
        return self.statements[name].execute()

    def deallocate(self, name):
        self.statements.pop(name, None)
