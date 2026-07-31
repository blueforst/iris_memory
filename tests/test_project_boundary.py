from iris_memory.project import PROJECT_BOUNDARY


def test_project_boundary_separates_agent_runtime() -> None:
    assert PROJECT_BOUNDARY.project == "iris-memory"
    assert "memory-router" in PROJECT_BOUNDARY.owns
    assert "memory-contracts" in PROJECT_BOUNDARY.owns
    assert "pi-session" in PROJECT_BOUNDARY.excludes
    assert "historian-database" in PROJECT_BOUNDARY.excludes
