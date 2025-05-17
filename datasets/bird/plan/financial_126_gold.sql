{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "FLOAT",
      "operands": [
        {
          "kind": "TIMES",
          "operator": "*",
          "type": "REAL",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "REAL",
              "operands": [
                {
                  "kind": "MINUS",
                  "operator": "-",
                  "type": "FLOAT",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 23,
                      "name": "$23",
                      "type": "FLOAT"
                    },
                    {
                      "kind": "INPUT_REF",
                      "index": 22,
                      "name": "$22",
                      "type": "FLOAT"
                    }
                  ]
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": 100,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            }
          ]
        },
        {
          "kind": "CASE",
          "operator": "CASE",
          "type": "FLOAT",
          "operands": [
            {
              "kind": "EQUALS",
              "operator": "=",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 22,
                  "name": "$22",
                  "type": "FLOAT"
                },
                {
                  "kind": "LITERAL",
                  "value": 0.0,
                  "type": "FLOAT",
                  "nullable": false,
                  "precision": 15
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": "NULL",
              "type": "FLOAT",
              "nullable": true,
              "precision": 15
            },
            {
              "kind": "INPUT_REF",
              "index": 22,
              "name": "$22",
              "type": "FLOAT"
            }
          ]
        }
      ]
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
            "index": 6,
            "name": "$6",
            "type": "VARCHAR"
          },
          {
            "kind": "LITERAL",
            "value": "D",
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
                "index": 8,
                "name": "$8",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 11,
                "name": "$11",
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
                    "index": 1,
                    "name": "$1",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 7,
                    "name": "$7",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "loan",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "account",
                  "id": "1",
                  "inputs": []
                }
              ]
            },
            {
              "relOp": "LogicalTableScan",
              "table": "district",
              "id": "3",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}