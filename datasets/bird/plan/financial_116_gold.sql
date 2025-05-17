{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "VARCHAR"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "7",
  "inputs": [
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
                      "index": 1,
                      "name": "$1",
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
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "VARCHAR"
        }
      ],
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
                  "kind": "INPUT_REF",
                  "index": 7,
                  "name": "$7",
                  "type": "VARCHAR"
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
                          "index": 1,
                          "name": "$1",
                          "type": "VARCHAR"
                        },
                        {
                          "kind": "LITERAL",
                          "value": "M",
                          "type": "VARCHAR",
                          "nullable": false,
                          "precision": -1
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
                        "value": "south Bohemia",
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