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
                  "type": "FLOAT"
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
                  "type": "FLOAT"
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
      ]
    },
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
                  "index": 2,
                  "name": "$2",
                  "type": "FLOAT"
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
                  "index": 3,
                  "name": "$3",
                  "type": "FLOAT"
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
      ]
    },
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
                  "index": 3,
                  "name": "$3",
                  "type": "FLOAT"
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
                  "index": 0,
                  "name": "$0",
                  "type": "FLOAT"
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
      ]
    }
  ],
  "id": "9",
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
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
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
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 2,
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
          "name": null
        },
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 3,
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
          "name": null
        }
      ],
      "id": "8",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
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
                      "index": 1,
                      "name": "$1",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "SME",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 5,
                  "name": "$5",
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
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "INTEGER"
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
                      "index": 1,
                      "name": "$1",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "LAM",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 5,
                  "name": "$5",
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
                      "index": 1,
                      "name": "$1",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "KAM",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 5,
                  "name": "$5",
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
            }
          ],
          "id": "7",
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
                        "index": 2,
                        "name": "$2",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "CZK",
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
                        "index": 5,
                        "name": "$5",
                        "type": "FLOAT"
                      },
                      {
                        "kind": "SCALAR_QUERY",
                        "operator": "$SCALAR_QUERY",
                        "operands": [],
                        "query": [
                          {
                            "relOp": "LogicalAggregate",
                            "keys": [],
                            "aggs": [
                              {
                                "operator": "MIN",
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
                            "id": "2",
                            "inputs": [
                              {
                                "relOp": "LogicalProject",
                                "project": [
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 2,
                                    "name": "$2",
                                    "type": "FLOAT"
                                  }
                                ],
                                "id": "1",
                                "inputs": [
                                  {
                                    "relOp": "LogicalTableScan",
                                    "table": "yearmonth",
                                    "id": "0",
                                    "inputs": []
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
                    "kind": "GREATER_THAN_OR_EQUAL",
                    "operator": ">=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "INTEGER",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 4,
                            "name": "$4",
                            "type": "VARCHAR"
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": 201301,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  },
                  {
                    "kind": "LESS_THAN_OR_EQUAL",
                    "operator": "<=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "INTEGER",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 4,
                            "name": "$4",
                            "type": "VARCHAR"
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": 201312,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  }
                ]
              },
              "variableset": "[]",
              "id": "6",
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
                        "index": 3,
                        "name": "$3",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "5",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "customers",
                      "id": "3",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "yearmonth",
                      "id": "4",
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