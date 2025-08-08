{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "FLOAT",
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
                  "kind": "MINUS",
                  "operator": "-",
                  "type": "FLOAT",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 0,
                      "name": "$0",
                      "type": "FLOAT"
                    },
                    {
                      "kind": "INPUT_REF",
                      "index": 1,
                      "name": "$1",
                      "type": "FLOAT"
                    }
                  ]
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
              "index": 1,
              "name": "$1",
              "type": "FLOAT"
            }
          ]
        }
      ]
    },
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "FLOAT",
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
                  "kind": "MINUS",
                  "operator": "-",
                  "type": "FLOAT",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 2,
                      "name": "$2",
                      "type": "FLOAT"
                    },
                    {
                      "kind": "INPUT_REF",
                      "index": 3,
                      "name": "$3",
                      "type": "FLOAT"
                    }
                  ]
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
          "type": "FLOAT",
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
              "index": 3,
              "name": "$3",
              "type": "FLOAT"
            }
          ]
        }
      ]
    },
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "FLOAT",
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
                  "kind": "MINUS",
                  "operator": "-",
                  "type": "FLOAT",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 4,
                      "name": "$4",
                      "type": "FLOAT"
                    },
                    {
                      "kind": "INPUT_REF",
                      "index": 5,
                      "name": "$5",
                      "type": "FLOAT"
                    }
                  ]
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
          "type": "FLOAT",
          "operands": [
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
              "index": 5,
              "name": "$5",
              "type": "FLOAT"
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
              "column": 1,
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
        },
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 4,
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
              "column": 5,
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
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
              "type": "FLOAT",
              "operands": [
                {
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
                      "kind": "LIKE",
                      "operator": "LIKE",
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
                          "value": "2013%",
                          "type": "CHAR",
                          "nullable": false,
                          "precision": 5
                        }
                      ]
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
                      "kind": "LIKE",
                      "operator": "LIKE",
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
                          "value": "2012%",
                          "type": "CHAR",
                          "nullable": false,
                          "precision": 5
                        }
                      ]
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
                      "kind": "LIKE",
                      "operator": "LIKE",
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
                          "value": "2013%",
                          "type": "CHAR",
                          "nullable": false,
                          "precision": 5
                        }
                      ]
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
                      "kind": "LIKE",
                      "operator": "LIKE",
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
                          "value": "2012%",
                          "type": "CHAR",
                          "nullable": false,
                          "precision": 5
                        }
                      ]
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
                      "kind": "LIKE",
                      "operator": "LIKE",
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
                          "value": "2013%",
                          "type": "CHAR",
                          "nullable": false,
                          "precision": 5
                        }
                      ]
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
                      "kind": "LIKE",
                      "operator": "LIKE",
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
                          "value": "2012%",
                          "type": "CHAR",
                          "nullable": false,
                          "precision": 5
                        }
                      ]
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
                    "index": 3,
                    "name": "$3",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "customers",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "yearmonth",
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