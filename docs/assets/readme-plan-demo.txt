                                              TokenKick plan --work-window 18:30-23:30                                              
╭─────┬───────────────────┬─────────┬───────────────┬─────────────┬────────────────────────────────────────────────────────────────╮
│ Use │ Account           │ State   │ Best action   │ Coverage    │ Reason                                                         │
├─────┼───────────────────┼─────────┼───────────────┼─────────────┼────────────────────────────────────────────────────────────────┤
│ 1   │ codex (work)      │ Active  │ Use now       │ 18:30-20:55 │ Session already counting down; avoid spending a fresh window.  │
│ 2   │ codex-spark (lab) │ Fresh   │ Kick at 20:55 │ 20:55-22:25 │ Short Spark window fills the middle gap after explicit tier    │
│     │                   │         │               │             │ config.                                                        │
│ 3   │ claude (personal) │ Waiting │ Kick at 22:25 │ 22:25-23:30 │ Reset lands late; pending kick is scheduled inside the work    │
│     │                   │         │               │             │ window.                                                        │
╰─────┴───────────────────┴─────────┴───────────────┴─────────────┴────────────────────────────────────────────────────────────────╯

Plan result  3 accounts cover 5h 0m with 0m projected waste.
Apply only after review: TK_NO_INTERACTIVE=1 tk plan --work-window 18:30-23:30 --apply --yes --json-output
Synthetic demo data. Planning reads cached state and does not kick by itself.
