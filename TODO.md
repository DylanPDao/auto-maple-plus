# TO DO LIST
1. Detect when the character is in town (sets `config.char_in_town` for "Auto pause in town") — needs a template image
2. demonavenger's command book imports `press` from `interception`, whose `press()` doesn't accept `up_time` (used by `Execution`). Fixed only as an uncommitted local edit inside the `resources/` submodule (import from `src.common.vkeys` instead) — point the submodule at a fork of auto-maple-resources to commit it
