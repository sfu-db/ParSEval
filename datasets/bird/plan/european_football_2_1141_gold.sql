{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 20,
      "name": "$20",
      "type": "INTEGER"
    },
    {
      "kind": "INPUT_REF",
      "index": 21,
      "name": "$21",
      "type": "INTEGER"
    },
    {
      "kind": "INPUT_REF",
      "index": 19,
      "name": "$19",
      "type": "INTEGER"
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
            "index": 2,
            "name": "$2",
            "type": "INTEGER"
          }
        ],
        "query": [
          {
            "relOp": "LogicalProject",
            "project": [
              {
                "kind": "INPUT_REF",
                "index": 1,
                "name": "$1",
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
                      "index": 2,
                      "name": "$2",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "Alexis Blin",
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
                    "table": "Player",
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
          "table": "Player_Attributes",
          "id": "3",
          "inputs": []
        }
      ]
    }
  ]
}