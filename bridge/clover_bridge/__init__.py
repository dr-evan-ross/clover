"""CLOVER bridge: sits between the medic GUI (WebSocket) and a ventilator (serial).

Core modules are standard-library only so they can be tested anywhere:
  adapter.py     - the VentAdapter interface your vendor-specific adapter implements
  stub_adapter.py- a simulated ventilator + patient that implements it
  controller.py  - the CLOVER decision logic with reasoning strings
  session.py     - session state (mode, overrides, targets, log) and frame building
Network/serial live in server.py and need the packages in requirements.txt.
"""
__version__ = "0.1.0"
