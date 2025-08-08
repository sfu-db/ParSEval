{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "DATE"
    },
    {
      "column": 2,
      "type": "INTEGER"
    }
  ],
  "dir": [
    "ASCENDING",
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "7",
  "inputs": [
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
          "index": 2,
          "name": "$2",
          "type": "DATE"
        },
        {
          "kind": "INPUT_REF",
          "index": 14,
          "name": "$14",
          "type": "INTEGER"
        }
      ],
      "id": "6",
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
                "index": 1,
                "name": "$1",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "F",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          "variableset": "[]",
          "id": "5",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 4,
                    "name": "$4",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 21,
                    "name": "$21",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "4",
              "inputs": [
                {
                  "relOp": "LogicalJoin",
                  "joinType": "inner",
                  "condition": {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 3,
                        "name": "$3",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 4,
                        "name": "$4",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "client",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "district",
                      "id": "1",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "account",
                  "id": "3",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}