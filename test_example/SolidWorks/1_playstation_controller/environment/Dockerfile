# SolidWorks cannot be installed, licensed, or driven headlessly in a
# Docker container: it requires a live GUI session on a Windows host with
# a purchased/activated SolidWorks license, driven through the COM API
# (pywin32's win32com.client.Dispatch("SldWorks.Application")).
#
# Harbor's Windows container support explicitly has no display forwarding
# or input simulation for GUI apps -- see:
# https://www.harborframework.com/docs/tasks/windows-container-support#command-execution
#
# This task is NOT runnable through `harbor run` in this repo checkout.
# Run it locally on a Windows machine with SolidWorks installed instead:
#   1. Open environment/input.SLDPRT in SolidWorks
#   2. Apply the edit described in instruction.md
#   3. Save, then run: python tests/task/harness/harness.py <path-to-saved-part>
#      (or with no argv[1] to grade the currently active/open document)
#
# TODO: FROM mcr.microsoft.com/windows/servercore:ltsc2022  -- or whatever
#       base the target Windows host's SolidWorks install actually needs
#       (SolidWorks has no supported Windows Server deployment story; a
#       real client OS image may be required instead of servercore).
# TODO: install SolidWorks itself (license required -- cannot be scripted
#       or verified from this environment).
# TODO: install Python + pywin32 so tests/task/harness/harness.py and
#       common/solidworks_measure.py, common/solidworks_capture.py,
#       common/solidworks_session.py are runnable against the live COM
#       session.
