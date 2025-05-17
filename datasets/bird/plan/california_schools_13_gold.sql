{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "MAX",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "FLOAT"
        }
      ],
      "type": "FLOAT",
      "name": "EXPR$0"
    }
  ],
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
                  "index": 24,
                  "name": "$24",
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
                      "index": 23,
                      "name": "$23",
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
                  "index": 23,
                  "name": "$23",
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
            "kind": "GREATER_THAN",
            "operator": ">",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "DIVIDE",
                "operator": "/",
                "type": "REAL",
                "operands": [
                  {
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "REAL",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 39,
                        "name": "$39",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  {
                    "kind": "CASE",
                    "operator": "CASE",
                    "type": "INTEGER",
                    "operands": [
                      {
                        "kind": "EQUALS",
                        "operator": "=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 35,
                            "name": "$35",
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
                      },
                      {
                        "kind": "LITERAL",
                        "value": "NULL",
                        "type": "INTEGER",
                        "nullable": true,
                        "precision": 10
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 35,
                        "name": "$35",
                        "type": "INTEGER"
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": 0.3,
                "type": "DECIMAL",
                "nullable": false,
                "precision": 2
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
                  "table": "satscores",
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