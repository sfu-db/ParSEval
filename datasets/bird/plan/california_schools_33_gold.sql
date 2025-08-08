{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "FLOAT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 5,
  "id": "5",
  "inputs": [
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
                  "index": 21,
                  "name": "$21",
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
                      "index": 18,
                      "name": "$18",
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
                  "index": 18,
                  "name": "$18",
                  "type": "FLOAT"
                }
              ]
            }
          ]
        },
        {
          "kind": "INPUT_REF",
          "index": 21,
          "name": "$21",
          "type": "FLOAT"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "INTEGER",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 56,
                    "name": "$56",
                    "type": "VARCHAR"
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": 66,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
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
                    "index": 29,
                    "name": "$29",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "frpm",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "schools",
                  "id": "1",
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