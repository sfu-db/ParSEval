{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 2,
      "type": "BIGINT"
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
                      "kind": "LITERAL",
                      "value": 4,
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
                  "kind": "LITERAL",
                  "value": "NULL",
                  "type": "INTEGER",
                  "nullable": true,
                  "precision": 10
                },
                {
                  "kind": "LITERAL",
                  "value": 4,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                }
              ]
            }
          ]
        },
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 4,
          "name": "$4",
          "type": "BIGINT"
        }
      ],
      "id": "6",
      "inputs": [
        {
          "relOp": "LogicalAggregate",
          "keys": [
            {
              "column": 0,
              "type": "DATE"
            },
            {
              "column": 1,
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
              "operands": [],
              "type": "BIGINT",
              "name": null
            },
            {
              "operator": "COUNT",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 1,
                  "type": "VARCHAR"
                }
              ],
              "type": "BIGINT",
              "name": "DESC"
            }
          ],
          "id": "5",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 17,
                  "name": "$17",
                  "type": "DATE"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 22,
                  "name": "$22",
                  "type": "VARCHAR"
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
                        "kind": "GREATER_THAN_OR_EQUAL",
                        "operator": ">=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 17,
                            "name": "$17",
                            "type": "DATE"
                          },
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "DATE",
                            "operands": [
                              {
                                "kind": "LITERAL",
                                "value": "2012-01-01",
                                "type": "CHAR",
                                "nullable": false,
                                "precision": 10
                              }
                            ]
                          }
                        ]
                      },
                      {
                        "kind": "LESS_THAN_OR_EQUAL",
                        "operator": "<=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 17,
                            "name": "$17",
                            "type": "DATE"
                          },
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "DATE",
                            "operands": [
                              {
                                "kind": "LITERAL",
                                "value": "2015-12-31",
                                "type": "CHAR",
                                "nullable": false,
                                "precision": 10
                              }
                            ]
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
                            "type": "INTEGER"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 21,
                            "name": "$21",
                            "type": "INTEGER"
                          }
                        ]
                      },
                      "id": "2",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "sets",
                          "id": "0",
                          "inputs": []
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "set_translations",
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