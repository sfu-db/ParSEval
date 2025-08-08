{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 1,
      "name": "$1",
      "type": "VARCHAR"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "IN",
        "operator": "IN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 0,
            "name": "$0",
            "type": "INTEGER"
          }
        ],
        "query": [
          {
            "relOp": "LogicalProject",
            "project": [
              {
                "kind": "INPUT_REF",
                "index": 0,
                "name": "$0",
                "type": "INTEGER"
              }
            ],
            "id": "2",
            "inputs": [
              {
                "relOp": "LogicalFilter",
                "condition": {
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 15,
                      "name": "$15",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "Battlebond",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                "variableset": "[]",
                "id": "1",
                "inputs": [
                  {
                    "relOp": "LogicalTableScan",
                    "table": "sets",
                    "id": "0",
                    "inputs": []
                  }
                ]
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "set_translations",
          "id": "3",
          "inputs": []
        }
      ]
    }
  ]
}