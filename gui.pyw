# Launch gui.py via pythonw so no console window appears.
# Double-click this file (or associate .pyw with pythonw.exe) to open the GUI.
import runpy, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
runpy.run_module("gui", run_name="__main__", alter_sys=True)
