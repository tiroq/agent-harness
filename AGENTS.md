# Harness integration contract

The host repository must keep task packages under `.scratch/` and workflow artifacts under `.copilot-workflow/`. Do not edit task source to hide a failed implementation. Do not mark `done` until the commit and post-commit audit are verified.

Use the generic lifecycle in `templates/deliver-task.prompt.md`. Project-specific agents may specialize implementation, review, or testing, but may not weaken the lifecycle or safety deny-list.
