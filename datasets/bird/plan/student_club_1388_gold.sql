{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 22,
      "name": "$22",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 23,
      "name": "$23",
      "type": "VARCHAR"
    }
  ],
  "id": "8",
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
            "value": "Yearly Kickoff",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
          }
        ]
      },
      "variableset": "[]",
      "id": "7",
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
                "index": 19,
                "name": "$19",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 21,
                "name": "$21",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "6",
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
                    "index": 7,
                    "name": "$7",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 20,
                    "name": "$20",
                    "type": "VARCHAR"
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
                        "index": 0,
                        "name": "$0",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 13,
                        "name": "$13",
                        "type": "VARCHAR"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "event",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "budget",
                      "id": "1",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "expense",
                  "id": "3",
                  "inputs": []
                }
              ]
            },
            {
              "relOp": "LogicalTableScan",
              "table": "member",
              "id": "5",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}