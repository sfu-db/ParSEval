{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "7",
  "inputs": [
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
      "id": "6",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "AND",
            "operator": "AND",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "LESS_THAN",
                "operator": "<",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 28,
                    "name": "$28",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 14,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "LESS_THAN",
                "operator": "<",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 63,
                    "name": "$63",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 3,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "GREATER_THAN",
                "operator": ">",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 63,
                    "name": "$63",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 0,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
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
                    "index": 0,
                    "name": "$0",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 51,
                    "name": "$51",
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
                        "index": 0,
                        "name": "$0",
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
                      "table": "Patient",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "Laboratory",
                      "id": "1",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Examination",
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