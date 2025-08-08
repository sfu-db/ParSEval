{
  "relOp": "LogicalFilter",
  "condition": {
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
            "index": 1,
            "name": "$1",
            "type": "REAL"
          }
        ]
      }
    ]
  },
  "variableset": "[]",
  "id": "9",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [
        {
          "column": 0,
          "type": "INTEGER"
        }
      ],
      "aggs": [
        {
          "operator": "AVG",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
              "type": "REAL"
            }
          ],
          "type": "REAL",
          "name": "EXPR$1"
        }
      ],
      "id": "8",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "INTEGER"
            },
            {
              "kind": "INPUT_REF",
              "index": 2,
              "name": "$2",
              "type": "REAL"
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
                },
                {
                  "kind": "INPUT_REF",
                  "index": 1,
                  "name": "$1",
                  "type": "INTEGER"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 3,
                  "name": "$3",
                  "type": "REAL"
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
                        "index": 2,
                        "name": "$2",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "LITERAL",
                        "value": 1,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  },
                  "variableset": "[]",
                  "id": "5",
                  "inputs": [
                    {
                      "relOp": "LogicalProject",
                      "project": [
                        {
                          "kind": "INPUT_REF",
                          "index": 19,
                          "name": "$19",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "INPUT_REF",
                          "index": 18,
                          "name": "$18",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "INPUT_REF",
                          "index": 8,
                          "name": "$8",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "CASE",
                          "operator": "CASE",
                          "type": "REAL",
                          "operands": [
                            {
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
                                  "kind": "LITERAL",
                                  "value": 1,
                                  "type": "INTEGER",
                                  "nullable": false,
                                  "precision": 10
                                }
                              ]
                            },
                            {
                              "kind": "PLUS",
                              "operator": "+",
                              "type": "REAL",
                              "operands": [
                                {
                                  "kind": "PLUS",
                                  "operator": "+",
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
                                              "kind": "OTHER_FUNCTION",
                                              "operator": "SUBSTR",
                                              "type": "VARCHAR",
                                              "operands": [
                                                {
                                                  "kind": "INPUT_REF",
                                                  "index": 11,
                                                  "name": "$11",
                                                  "type": "VARCHAR"
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
                                                  "value": 1,
                                                  "type": "INTEGER",
                                                  "nullable": false,
                                                  "precision": 10
                                                }
                                              ]
                                            }
                                          ]
                                        },
                                        {
                                          "kind": "LITERAL",
                                          "value": 3600,
                                          "type": "INTEGER",
                                          "nullable": false,
                                          "precision": 10
                                        }
                                      ]
                                    },
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
                                              "kind": "OTHER_FUNCTION",
                                              "operator": "SUBSTR",
                                              "type": "VARCHAR",
                                              "operands": [
                                                {
                                                  "kind": "INPUT_REF",
                                                  "index": 11,
                                                  "name": "$11",
                                                  "type": "VARCHAR"
                                                },
                                                {
                                                  "kind": "LITERAL",
                                                  "value": 3,
                                                  "type": "INTEGER",
                                                  "nullable": false,
                                                  "precision": 10
                                                },
                                                {
                                                  "kind": "LITERAL",
                                                  "value": 2,
                                                  "type": "INTEGER",
                                                  "nullable": false,
                                                  "precision": 10
                                                }
                                              ]
                                            }
                                          ]
                                        },
                                        {
                                          "kind": "LITERAL",
                                          "value": 60,
                                          "type": "INTEGER",
                                          "nullable": false,
                                          "precision": 10
                                        }
                                      ]
                                    }
                                  ]
                                },
                                {
                                  "kind": "CAST",
                                  "operator": "CAST",
                                  "type": "REAL",
                                  "operands": [
                                    {
                                      "kind": "OTHER_FUNCTION",
                                      "operator": "SUBSTR",
                                      "type": "VARCHAR",
                                      "operands": [
                                        {
                                          "kind": "INPUT_REF",
                                          "index": 11,
                                          "name": "$11",
                                          "type": "VARCHAR"
                                        },
                                        {
                                          "kind": "LITERAL",
                                          "value": 6,
                                          "type": "INTEGER",
                                          "nullable": false,
                                          "precision": 10
                                        }
                                      ]
                                    }
                                  ]
                                }
                              ]
                            },
                            {
                              "kind": "CAST",
                              "operator": "CAST",
                              "type": "REAL",
                              "operands": [
                                {
                                  "kind": "OTHER_FUNCTION",
                                  "operator": "SUBSTR",
                                  "type": "VARCHAR",
                                  "operands": [
                                    {
                                      "kind": "INPUT_REF",
                                      "index": 11,
                                      "name": "$11",
                                      "type": "VARCHAR"
                                    },
                                    {
                                      "kind": "LITERAL",
                                      "value": 2,
                                      "type": "INTEGER",
                                      "nullable": false,
                                      "precision": 10
                                    }
                                  ]
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
          ]
        }
      ]
    }
  ]
}