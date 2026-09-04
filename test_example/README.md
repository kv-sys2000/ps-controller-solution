# SolidWorks RL Environment — PlayStation Controller

A test RL environment containing a single SolidWorks CAD task: `SolidWorks/1_playstation_controller`. The goal of this repo is to **update — and potentially completely rewrite — the currently AI-generated grading harness** for the task: the script that scores a candidate `.SLDPRT` part against the task's rubric. The current harness at `tests/task/harness/harness.py` is the starting point, not a fixed reference; treat it as replaceable so long as the grading contract below is honored.

## First step: fetch the assets

The (very) large `.SLDPRT` files (input part, solution, examples) live in Azure Blob Storage, not git — the checkout only has their URLs and checksums in `task.toml`. Before doing anything else, run:

```bash
pip install -r env_requirements.txt   # pywin32 installs on Windows only
python3 tools/fetch.py
```

This downloads every missing or checksum-mismatched asset into place. Nothing in this repo works without it.

## The task

See `SolidWorks/1_playstation_controller/instruction.md` for the exact prompt.

## Layout of a task

Inside `SolidWorks/1_playstation_controller/`:

| Path | Purpose |
|---|---|
| `instruction.md` | The prompt. |
| `task.toml` | Task definition: metadata, scoring components, asset URLs/checksums, environment config. |
| `environment/` | The starting part (`input.SLDPRT`) and a (non-runnable, see below) Dockerfile. |
| `solution/` | The reference answer (`solution.SLDPRT`) — the "right" edit, which should score full marks. |
| `examples/` | Candidate parts to **test the harness against**: mostly adversarial near-misses (widened but clusters unmoved, naive geometric flip, 30 mm instead of 15, rebuild-error-riddled trees, …). |
| `tests/` | The harness to update/rewrite (`tests/task/harness/harness.py`), along with frozen baseline measurements of the input part (`tests/task/prompt/input.json`) and the verifier entrypoint (`test.sh`). |

Shared measurement/capture/scoring code used by harnesses lives in `common/` (`solidworks_measure.py`, `solidworks_capture.py`, `harness_base.py`, …).

## The harness

The harness grades **geometry only** (mass properties, centroids, face areas), never feature or body names — candidates may remodel freely. It scores against the components declared in `task.toml` (`[[metadata.scoring_components]]`) and prints a JSON score envelope (`{"score", "max_score", "passed", "subscores"}`; see `common/harness_base.py`). The scoring components themselves are also yours to edit: if you split, merge, reweight, or add components while reworking the harness, update `[[metadata.scoring_components]]` in `task.toml` to match — the two must stay in sync.

To validate a harness, run it over `solution/solution.SLDPRT` (should score full marks) and every file in `examples/`.

## Running it — Windows + SolidWorks required

SolidWorks cannot run headlessly or in a container: the harness drives a live, licensed SolidWorks session on Windows via COM (`pywin32`). Run it locally on a Windows machine with SolidWorks installed (you can also use WSL):

```bat
cd SolidWorks\1_playstation_controller
python tests\task\harness\harness.py path\to\candidate.SLDPRT
```
