{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "FLOAT",
      "operands": [
        {
          "kind": "CAST",
          "operator": "CAST",
          "type": "REAL",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 73,
              "name": "$73",
              "type": "FLOAT"
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
                  "index": 72,
                  "name": "$72",
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
              "index": 72,
              "name": "$72",
              "type": "FLOAT"
            }
          ]
        }
      ]
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 39,
                "name": "$39",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Kacey",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
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
                "index": 40,
                "name": "$40",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Gibson",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "3",
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
                "index": 49,
                "name": "$49",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "schools",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "frpm",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}