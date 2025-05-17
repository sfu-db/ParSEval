{
  "relOp": "LogicalProject",
  "project": [
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
                  "index": 0,
                  "name": "$0",
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
                      "index": 1,
                      "name": "$1",
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
                      "index": 1,
                      "name": "$1",
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
                  "index": 2,
                  "name": "$2",
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
                      "index": 3,
                      "name": "$3",
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
                      "index": 3,
                      "name": "$3",
                      "type": "BIGINT"
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [],
      "aggs": [
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 0,
              "type": "INTEGER"
            }
          ],
          "type": "INTEGER",
          "name": null
        },
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
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
              "column": 2,
              "type": "INTEGER"
            }
          ],
          "type": "INTEGER",
          "name": null
        },
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 3,
              "type": "INTEGER"
            }
          ],
          "type": "BIGINT",
          "name": null
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
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
                      "index": 2,
                      "name": "$2",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "Abdou Diallo",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 25,
                  "name": "$25",
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
                      "index": 2,
                      "name": "$2",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "Abdou Diallo",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "CAST",
                  "operator": "CAST",
                  "type": "INTEGER",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 7,
                      "name": "$7",
                      "type": "INTEGER"
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": "NULL",
                  "type": "INTEGER",
                  "nullable": true,
                  "precision": 10
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
                      "index": 2,
                      "name": "$2",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "Aaron Appindangoye",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 25,
                  "name": "$25",
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
                      "index": 2,
                      "name": "$2",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "Aaron Appindangoye",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "CAST",
                  "operator": "CAST",
                  "type": "INTEGER",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 7,
                      "name": "$7",
                      "type": "INTEGER"
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": "NULL",
                  "type": "INTEGER",
                  "nullable": true,
                  "precision": 10
                }
              ]
            }
          ],
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
                    "index": 1,
                    "name": "$1",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 9,
                    "name": "$9",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Player",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Player_Attributes",
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