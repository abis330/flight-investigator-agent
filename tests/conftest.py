import pytest
from flightlake.lake import Lake
from flightlake.sim.writer import generate_fleet


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("inv")
    paths = generate_fleet(14, root / "raw", seed=3, fault_rate=0.8)
    Lake(root / "lake").ingest_many(paths, workers=1)
    from flightinvestigator.incidents import build

    build(str(root / "lake"), str(root / "data" / "incidents.json"))
    return {"lake": str(root / "lake"), "incidents": str(root / "data" / "incidents.json")}
