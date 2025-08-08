{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 4,
      "name": "$4",
      "type": "VARCHAR"
    }
  ],
  "id": "17",
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
            "index": 4,
            "name": "$4",
            "type": "VARCHAR"
          },
          {
            "kind": "LITERAL",
            "value": "Austrian Grand Prix",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
          }
        ]
      },
      "variableset": "[]",
      "id": "16",
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
                                    "index": 32,
                                    "name": "$32",
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
                                    "kind": "MINUS",
                                    "operator": "-",
                                    "type": "INTEGER",
                                    "operands": [
                                      {
                                        "kind": "OTHER_FUNCTION",
                                        "operator": "INSTR",
                                        "type": "INTEGER",
                                        "operands": [
                                          {
                                            "kind": "INPUT_REF",
                                            "index": 32,
                                            "name": "$32",
                                            "type": "VARCHAR"
                                          },
                                          {
                                            "kind": "LITERAL",
                                            "value": ":",
                                            "type": "CHAR",
                                            "nullable": false,
                                            "precision": 1
                                          }
                                        ]
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
                                "index": 32,
                                "name": "$32",
                                "type": "VARCHAR"
                              },
                              {
                                "kind": "PLUS",
                                "operator": "+",
                                "type": "INTEGER",
                                "operands": [
                                  {
                                    "kind": "OTHER_FUNCTION",
                                    "operator": "INSTR",
                                    "type": "INTEGER",
                                    "operands": [
                                      {
                                        "kind": "INPUT_REF",
                                        "index": 32,
                                        "name": "$32",
                                        "type": "VARCHAR"
                                      },
                                      {
                                        "kind": "LITERAL",
                                        "value": ":",
                                        "type": "CHAR",
                                        "nullable": false,
                                        "precision": 1
                                      }
                                    ]
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
                                "kind": "MINUS",
                                "operator": "-",
                                "type": "INTEGER",
                                "operands": [
                                  {
                                    "kind": "MINUS",
                                    "operator": "-",
                                    "type": "INTEGER",
                                    "operands": [
                                      {
                                        "kind": "OTHER_FUNCTION",
                                        "operator": "INSTR",
                                        "type": "INTEGER",
                                        "operands": [
                                          {
                                            "kind": "INPUT_REF",
                                            "index": 32,
                                            "name": "$32",
                                            "type": "VARCHAR"
                                          },
                                          {
                                            "kind": "LITERAL",
                                            "value": ".",
                                            "type": "CHAR",
                                            "nullable": false,
                                            "precision": 1
                                          }
                                        ]
                                      },
                                      {
                                        "kind": "OTHER_FUNCTION",
                                        "operator": "INSTR",
                                        "type": "INTEGER",
                                        "operands": [
                                          {
                                            "kind": "INPUT_REF",
                                            "index": 32,
                                            "name": "$32",
                                            "type": "VARCHAR"
                                          },
                                          {
                                            "kind": "LITERAL",
                                            "value": ":",
                                            "type": "CHAR",
                                            "nullable": false,
                                            "precision": 1
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
                            "kind": "OTHER_FUNCTION",
                            "operator": "SUBSTR",
                            "type": "VARCHAR",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 32,
                                "name": "$32",
                                "type": "VARCHAR"
                              },
                              {
                                "kind": "PLUS",
                                "operator": "+",
                                "type": "INTEGER",
                                "operands": [
                                  {
                                    "kind": "OTHER_FUNCTION",
                                    "operator": "INSTR",
                                    "type": "INTEGER",
                                    "operands": [
                                      {
                                        "kind": "INPUT_REF",
                                        "index": 32,
                                        "name": "$32",
                                        "type": "VARCHAR"
                                      },
                                      {
                                        "kind": "LITERAL",
                                        "value": ".",
                                        "type": "CHAR",
                                        "nullable": false,
                                        "precision": 1
                                      }
                                    ]
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
                                "value": 1000,
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
                            "value": 1000,
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
                "kind": "INPUT_REF",
                "index": 35,
                "name": "$35",
                "type": "REAL"
              }
            ]
          },
          "id": "15",
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
                    "index": 18,
                    "name": "$18",
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
                        "index": 3,
                        "name": "$3",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 8,
                        "name": "$8",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "races",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "circuits",
                      "id": "1",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "results",
                  "id": "3",
                  "inputs": []
                }
              ]
            },
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
                      "type": "REAL"
                    }
                  ],
                  "type": "REAL",
                  "name": "min_time_in_seconds"
                }
              ],
              "id": "14",
              "inputs": [
                {
                  "relOp": "LogicalProject",
                  "project": [
                    {
                      "kind": "INPUT_REF",
                      "index": 2,
                      "name": "$2",
                      "type": "REAL"
                    }
                  ],
                  "id": "13",
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
                            "index": 7,
                            "name": "$7",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": "Austrian Grand Prix",
                            "type": "VARCHAR",
                            "nullable": false,
                            "precision": -1
                          }
                        ]
                      },
                      "variableset": "[]",
                      "id": "12",
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
                                "index": 6,
                                "name": "$6",
                                "type": "INTEGER"
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 11,
                                "name": "$11",
                                "type": "INTEGER"
                              }
                            ]
                          },
                          "id": "11",
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
                              "id": "9",
                              "inputs": [
                                {
                                  "relOp": "LogicalProject",
                                  "project": [
                                    {
                                      "kind": "INPUT_REF",
                                      "index": 1,
                                      "name": "$1",
                                      "type": "INTEGER"
                                    },
                                    {
                                      "kind": "INPUT_REF",
                                      "index": 15,
                                      "name": "$15",
                                      "type": "VARCHAR"
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
                                                          "index": 15,
                                                          "name": "$15",
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
                                                          "kind": "MINUS",
                                                          "operator": "-",
                                                          "type": "INTEGER",
                                                          "operands": [
                                                            {
                                                              "kind": "OTHER_FUNCTION",
                                                              "operator": "INSTR",
                                                              "type": "INTEGER",
                                                              "operands": [
                                                                {
                                                                  "kind": "INPUT_REF",
                                                                  "index": 15,
                                                                  "name": "$15",
                                                                  "type": "VARCHAR"
                                                                },
                                                                {
                                                                  "kind": "LITERAL",
                                                                  "value": ":",
                                                                  "type": "CHAR",
                                                                  "nullable": false,
                                                                  "precision": 1
                                                                }
                                                              ]
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
                                                      "index": 15,
                                                      "name": "$15",
                                                      "type": "VARCHAR"
                                                    },
                                                    {
                                                      "kind": "PLUS",
                                                      "operator": "+",
                                                      "type": "INTEGER",
                                                      "operands": [
                                                        {
                                                          "kind": "OTHER_FUNCTION",
                                                          "operator": "INSTR",
                                                          "type": "INTEGER",
                                                          "operands": [
                                                            {
                                                              "kind": "INPUT_REF",
                                                              "index": 15,
                                                              "name": "$15",
                                                              "type": "VARCHAR"
                                                            },
                                                            {
                                                              "kind": "LITERAL",
                                                              "value": ":",
                                                              "type": "CHAR",
                                                              "nullable": false,
                                                              "precision": 1
                                                            }
                                                          ]
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
                                                      "kind": "MINUS",
                                                      "operator": "-",
                                                      "type": "INTEGER",
                                                      "operands": [
                                                        {
                                                          "kind": "MINUS",
                                                          "operator": "-",
                                                          "type": "INTEGER",
                                                          "operands": [
                                                            {
                                                              "kind": "OTHER_FUNCTION",
                                                              "operator": "INSTR",
                                                              "type": "INTEGER",
                                                              "operands": [
                                                                {
                                                                  "kind": "INPUT_REF",
                                                                  "index": 15,
                                                                  "name": "$15",
                                                                  "type": "VARCHAR"
                                                                },
                                                                {
                                                                  "kind": "LITERAL",
                                                                  "value": ".",
                                                                  "type": "CHAR",
                                                                  "nullable": false,
                                                                  "precision": 1
                                                                }
                                                              ]
                                                            },
                                                            {
                                                              "kind": "OTHER_FUNCTION",
                                                              "operator": "INSTR",
                                                              "type": "INTEGER",
                                                              "operands": [
                                                                {
                                                                  "kind": "INPUT_REF",
                                                                  "index": 15,
                                                                  "name": "$15",
                                                                  "type": "VARCHAR"
                                                                },
                                                                {
                                                                  "kind": "LITERAL",
                                                                  "value": ":",
                                                                  "type": "CHAR",
                                                                  "nullable": false,
                                                                  "precision": 1
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
                                                  "kind": "OTHER_FUNCTION",
                                                  "operator": "SUBSTR",
                                                  "type": "VARCHAR",
                                                  "operands": [
                                                    {
                                                      "kind": "INPUT_REF",
                                                      "index": 15,
                                                      "name": "$15",
                                                      "type": "VARCHAR"
                                                    },
                                                    {
                                                      "kind": "PLUS",
                                                      "operator": "+",
                                                      "type": "INTEGER",
                                                      "operands": [
                                                        {
                                                          "kind": "OTHER_FUNCTION",
                                                          "operator": "INSTR",
                                                          "type": "INTEGER",
                                                          "operands": [
                                                            {
                                                              "kind": "INPUT_REF",
                                                              "index": 15,
                                                              "name": "$15",
                                                              "type": "VARCHAR"
                                                            },
                                                            {
                                                              "kind": "LITERAL",
                                                              "value": ".",
                                                              "type": "CHAR",
                                                              "nullable": false,
                                                              "precision": 1
                                                            }
                                                          ]
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
                                                      "value": 1000,
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
                                                  "value": 1000,
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
                                  "id": "7",
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
                                                "index": 15,
                                                "name": "$15",
                                                "type": "VARCHAR"
                                              }
                                            ]
                                          }
                                        ]
                                      },
                                      "variableset": "[]",
                                      "id": "6",
                                      "inputs": [
                                        {
                                          "relOp": "LogicalTableScan",
                                          "table": "results",
                                          "id": "5",
                                          "inputs": []
                                        }
                                      ]
                                    }
                                  ]
                                },
                                {
                                  "relOp": "LogicalTableScan",
                                  "table": "races",
                                  "id": "8",
                                  "inputs": []
                                }
                              ]
                            },
                            {
                              "relOp": "LogicalTableScan",
                              "table": "circuits",
                              "id": "10",
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