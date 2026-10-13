"""PyInstaller's entry script for ALICE.exe — the same thing the `alice` console command
runs (pyproject.toml's [project.scripts]), as a plain script PyInstaller can start from."""
import multiprocessing

from overlay.overlay_app import main

if __name__ == "__main__":
    # A no-op unless something in the dependency tree spawns worker processes — then a
    # frozen exe needs it, or each worker re-runs the whole app.
    multiprocessing.freeze_support()
    main()
