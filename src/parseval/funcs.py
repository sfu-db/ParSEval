{
    "functions": [
        {
            "name": "gt",
            "type": "branch",
            "symbolic": "args[0].gt(args[1])",
            "concrete": "args[0] > args[1]",
        },
        {"name": "strftime", "type": "opaque", "concrete": "args[0].strftime(args[1])"},
        {
            "name": "DATE_ADD",
            "type": "opaque",
            "symbolic": "args[0].gt(args[1])",
            "concrete": "args[0] > args[1]",
        },
    ]
}
