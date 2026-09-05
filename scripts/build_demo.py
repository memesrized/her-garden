"""Generate and execute the public demo against an isolated PostgreSQL schema."""

import subprocess
from pathlib import Path

import nbformat
from nbclient import NotebookClient


def main() -> None:
    """Write committed outputs without touching production or persisting credentials."""
    notebook = nbformat.v4.new_notebook()
    notebook.metadata.kernelspec = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }
    notebook.cells = [
        nbformat.v4.new_markdown_cell(
            "# Plant memory demo\n\n"
            "This adds durable plant and supply memory; previously the repository had only a spec. "
            "Events and current state share a transaction so a retry cannot leave conflicting facts. "
            "Corrections preserve the original event, and approximate quantities remain descriptive. "
            "Care advice stays with the LLM because the server's job is to remember reported facts.\n\n"
            "Input comes from `data/demo_garden.json`, using the spec's examples. No actual private "
            "plant data was provided, so this fixture is explicitly fictional. The demo uses an "
            "isolated schema in TEST_DATABASE_URL and removes that schema when finished."
        ),
        nbformat.v4.new_code_cell(
            "import json, os\nfrom pathlib import Path\nfrom uuid import UUID, uuid4\n"
            "from datetime import UTC, datetime, timedelta\nimport psycopg\n"
            "from psycopg import sql\nfrom psycopg.conninfo import make_conninfo\n"
            "from her_garden.store import GardenStore\n"
            "from her_garden.models import PlantState, PlantEvent, InventoryEvent\n\n"
            "root = Path.cwd()\n"
            "fixture = json.loads((root / 'data/demo_garden.json').read_text())\n"
            "print(fixture['provenance'])\n"
            "print('Plant:', fixture['plant']['name'], '| Location:', fixture['location'])"
        ),
        nbformat.v4.new_code_cell(
            "async def show_demo() -> None:\n"
            "    dsn = os.environ['TEST_DATABASE_URL']\n"
            "    schema = 'demo_' + uuid4().hex\n"
            "    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:\n"
            "        await conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))\n"
            "    store = GardenStore(make_conninfo(dsn, options=f'-csearch_path={schema}'))\n"
            "    await store.open()\n"
            "    try:\n"
            "        location = await store.create_location(uuid4(), fixture['location'])\n"
            "        plant = await store.create_plant(uuid4(), PlantState(**fixture['plant'], "
            "location_id=UUID(location['entity_id'])))\n"
            "        plant_id = UUID(plant['entity_id'])\n"
            "        now = datetime.now(UTC)\n"
            "        request_id = uuid4()\n"
            "        event = PlantEvent(event_type='watering', occurred_at=now-timedelta(days=2), "
            "note='Watered thoroughly')\n"
            "        saved = await store.append_plant_event(request_id, plant_id, event)\n"
            "        retry = await store.append_plant_event(request_id, plant_id, event)\n"
            "        print('Technical retry returns the same event:', saved == retry)\n"
            "        await store.append_plant_event(uuid4(), plant_id, PlantEvent(\n"
            "            event_type='watering', occurred_at=now-timedelta(days=9), "
            "note='Earlier watering reported later'))\n"
            "        context = await store.get_plant_context(plant_id)\n"
            "        print('Backdated report preserves latest watering:', "
            "context['latest_actions']['watering']['event_id'] == saved['event_id'])\n"
            "        await store.append_plant_event(uuid4(), plant_id, PlantEvent(\n"
            "            event_type='void', occurred_at=now, note='Actually watered another plant', "
            "supersedes_event_id=UUID(saved['event_id'])))\n"
            "        context = await store.get_plant_context(plant_id)\n"
            "        print('After correction:', context['latest_actions']['watering']['details']['note'])\n"
            "        print('Original history retained:', context['total_events'], 'events')\n"
            "        item = await store.append_inventory_event(uuid4(), None, InventoryEvent(\n"
            "            **fixture['inventory'], event_type='observation', occurred_at=now, "
            "note='Checked cupboard'))\n"
            "        print('Perlite remaining:', (await store.list_entities('inventory'))[0]['remaining'])\n"
            "        await store.append_inventory_event(uuid4(), UUID(item['entity_id']), InventoryEvent(\n"
            "            event_type='usage', occurred_at=now, note='Used some for repotting'))\n"
            "        print('After unspecified usage:', (await store.list_entities('inventory'))[0]['remaining'], "
            "'(unknown, not zero)')\n"
            "    finally:\n"
            "        await store.close()\n"
            "        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:\n"
            "            await conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))\n\n"
            "await show_demo()"
        ),
    ]
    NotebookClient(
        notebook, timeout=30, resources={"metadata": {"path": str(Path.cwd())}}
    ).execute()
    nbformat.write(notebook, "notebooks/demos/plant_memory.ipynb")
    subprocess.run(["ruff", "format", "notebooks/demos/plant_memory.ipynb"], check=True)


if __name__ == "__main__":
    main()
