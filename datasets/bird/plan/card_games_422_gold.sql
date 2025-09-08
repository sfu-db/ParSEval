{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 0,
      "type": "VARCHAR"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 3,
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 48,
          "name": "$48",
          "type": "VARCHAR"
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "AND",
            "operator": "AND",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "OR",
                "operator": "OR",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "IS_NULL",
                    "operator": "IS NULL",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 54,
                        "name": "$54",
                        "type": "VARCHAR"
                      }
                    ]
                  },
                  {
                    "kind": "LIKE",
                    "operator": "LIKE",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 54,
                        "name": "$54",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "%*%",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 3
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 56,
                    "name": "$56",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "arenaleague",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "cards",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}