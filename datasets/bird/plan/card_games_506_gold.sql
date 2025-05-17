{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 3,
      "name": "$3",
      "type": "VARCHAR"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "IN",
            "operator": "IN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 2,
                "name": "$2",
                "type": "VARCHAR"
              }
            ],
            "query": [
              {
                "relOp": "LogicalProject",
                "project": [
                  {
                    "kind": "INPUT_REF",
                    "index": 4,
                    "name": "$4",
                    "type": "VARCHAR"
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
                          "value": "Mirrodin",
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
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 1,
                "name": "$1",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Chinese Simplified",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
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