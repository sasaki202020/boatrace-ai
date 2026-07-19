# Prospective Stage A Autopilot

`BOATRACE-Prospective-Shadow-V2` runs at logon and every 15 minutes while the
user is logged on. Concurrent starts are rejected by both Task Scheduler and a
runtime lock. The controller reads only existing local B/K files and connects
externally only to the approved GitHub Contents endpoint through the existing
transport.

GitHub authentication is read from the GitHub CLI keyring using `gh auth token`
and passed only in the child process environment. Credentials are not written
to task arguments, source, reports, SQLite, or logs.

Each cycle processes at most one future B file, one venue, twelve races, one
package, and one create-only anchor. Results are later appended from the local
official K directory. ROI and profit are never calculated.

Stage A stops the scheduled task after both seven verified days and 300
verified races are reached, or immediately on an integrity-gate failure.
Payment, betting, profit claims, and production adoption remain disabled.
