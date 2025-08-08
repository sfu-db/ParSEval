{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "REAL",
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
                  "kind": "INPUT_REF",
                  "index": 0,
                  "name": "$0",
                  "type": "INTEGER"
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
    }
  ],
  "id": "6",
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
          "operands": [],
          "type": "BIGINT",
          "name": null
        }
      ],
      "id": "5",
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
                  "kind": "NOT",
                  "operator": "NOT",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "IS_NULL",
                      "operator": "IS NULL",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 11,
                          "name": "$11",
                          "type": "VARCHAR"
                        }
                      ]
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": 1,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
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
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "INTEGER"
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
                        "index": 22,
                        "name": "$22",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "Australian GrAND Prix",
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
                        "index": 19,
                        "name": "$19",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "LITERAL",
                        "value": 2008,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
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
                        "index": 1,
                        "name": "$1",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 18,
                        "name": "$18",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "results",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "races",
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