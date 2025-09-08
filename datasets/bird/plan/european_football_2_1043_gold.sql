{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "VARCHAR"
    }
  ],
  "id": "7",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "GREATER_THAN",
        "operator": ">",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "MINUS",
            "operator": "-",
            "type": "REAL",
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
                        "index": 1,
                        "name": "$1",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  {
                    "kind": "CASE",
                    "operator": "CASE",
                    "type": "BIGINT",
                    "operands": [
                      {
                        "kind": "EQUALS",
                        "operator": "=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 2,
                            "name": "$2",
                            "type": "BIGINT"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 0,
                            "type": "BIGINT",
                            "nullable": false,
                            "precision": 19
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "NULL",
                        "type": "BIGINT",
                        "nullable": true,
                        "precision": 19
                      },
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "BIGINT",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 2,
                            "name": "$2",
                            "type": "BIGINT"
                          }
                        ]
                      }
                    ]
                  }
                ]
              },
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
                        "index": 3,
                        "name": "$3",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  {
                    "kind": "CASE",
                    "operator": "CASE",
                    "type": "BIGINT",
                    "operands": [
                      {
                        "kind": "EQUALS",
                        "operator": "=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 2,
                            "name": "$2",
                            "type": "BIGINT"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 0,
                            "type": "BIGINT",
                            "nullable": false,
                            "precision": 19
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "NULL",
                        "type": "BIGINT",
                        "nullable": true,
                        "precision": 19
                      },
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "BIGINT",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 2,
                            "name": "$2",
                            "type": "BIGINT"
                          }
                        ]
                      }
                    ]
                  }
                ]
              }
            ]
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
      "variableset": "[]",
      "id": "6",
      "inputs": [
        {
          "relOp": "LogicalAggregate",
          "keys": [
            {
              "column": 0,
              "type": "VARCHAR"
            }
          ],
          "aggs": [
            {
              "operator": "SUM",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 1,
                  "type": "INTEGER"
                }
              ],
              "type": "INTEGER",
              "name": null
            },
            {
              "operator": "COUNT",
              "distinct": true,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 2,
                  "type": "INTEGER"
                }
              ],
              "type": "BIGINT",
              "name": null
            },
            {
              "operator": "SUM",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 3,
                  "type": "INTEGER"
                }
              ],
              "type": "INTEGER",
              "name": null
            }
          ],
          "id": "5",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 2,
                  "name": "$2",
                  "type": "VARCHAR"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 12,
                  "name": "$12",
                  "type": "INTEGER"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 3,
                  "name": "$3",
                  "type": "INTEGER"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 13,
                  "name": "$13",
                  "type": "INTEGER"
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
                        "kind": "INPUT_REF",
                        "index": 6,
                        "name": "$6",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "2009/2010",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
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
                            "type": "INTEGER"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 5,
                            "name": "$5",
                            "type": "INTEGER"
                          }
                        ]
                      },
                      "id": "2",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "League",
                          "id": "0",
                          "inputs": []
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "Match",
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
      ]
    }
  ]
}