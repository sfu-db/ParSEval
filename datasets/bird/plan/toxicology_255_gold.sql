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
                  "kind": "SCALAR_QUERY",
                  "operator": "$SCALAR_QUERY",
                  "operands": [],
                  "query": [
                    {
                      "relOp": "LogicalProject",
                      "project": [
                        {
                          "kind": "INPUT_REF",
                          "index": 0,
                          "name": "$0",
                          "type": "BIGINT"
                        }
                      ],
                      "id": "7",
                      "inputs": [
                        {
                          "relOp": "LogicalSort",
                          "sort": [
                            {
                              "column": 1,
                              "type": "BIGINT"
                            }
                          ],
                          "dir": [
                            "DESCENDING"
                          ],
                          "offset": 0,
                          "limit": 1,
                          "id": "6",
                          "inputs": [
                            {
                              "relOp": "LogicalProject",
                              "project": [
                                {
                                  "kind": "INPUT_REF",
                                  "index": 1,
                                  "name": "$1",
                                  "type": "BIGINT"
                                },
                                {
                                  "kind": "INPUT_REF",
                                  "index": 2,
                                  "name": "$2",
                                  "type": "BIGINT"
                                }
                              ],
                              "id": "5",
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
                                      "name": "EXPR$0"
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
                                  "id": "4",
                                  "inputs": [
                                    {
                                      "relOp": "LogicalProject",
                                      "project": [
                                        {
                                          "kind": "INPUT_REF",
                                          "index": 5,
                                          "name": "$5",
                                          "type": "VARCHAR"
                                        },
                                        {
                                          "kind": "INPUT_REF",
                                          "index": 0,
                                          "name": "$0",
                                          "type": "VARCHAR"
                                        },
                                        {
                                          "kind": "INPUT_REF",
                                          "index": 3,
                                          "name": "$3",
                                          "type": "VARCHAR"
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
                                                "index": 2,
                                                "name": "$2",
                                                "type": "VARCHAR"
                                              },
                                              {
                                                "kind": "INPUT_REF",
                                                "index": 3,
                                                "name": "$3",
                                                "type": "VARCHAR"
                                              }
                                            ]
                                          },
                                          "id": "2",
                                          "inputs": [
                                            {
                                              "relOp": "LogicalTableScan",
                                              "table": "connected",
                                              "id": "0",
                                              "inputs": []
                                            },
                                            {
                                              "relOp": "LogicalTableScan",
                                              "table": "bond",
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
                  "kind": "SCALAR_QUERY",
                  "operator": "$SCALAR_QUERY",
                  "operands": [],
                  "query": [
                    {
                      "relOp": "LogicalAggregate",
                      "keys": [],
                      "aggs": [
                        {
                          "operator": "COUNT",
                          "distinct": false,
                          "ignoreNulls": false,
                          "operands": [
                            {
                              "column": 0,
                              "type": "VARCHAR"
                            }
                          ],
                          "type": "BIGINT",
                          "name": "EXPR$0"
                        }
                      ],
                      "id": "10",
                      "inputs": [
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
                          "id": "9",
                          "inputs": [
                            {
                              "relOp": "LogicalTableScan",
                              "table": "connected",
                              "id": "8",
                              "inputs": []
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
              "kind": "SCALAR_QUERY",
              "operator": "$SCALAR_QUERY",
              "operands": [],
              "query": [
                {
                  "relOp": "LogicalAggregate",
                  "keys": [],
                  "aggs": [
                    {
                      "operator": "COUNT",
                      "distinct": false,
                      "ignoreNulls": false,
                      "operands": [
                        {
                          "column": 0,
                          "type": "VARCHAR"
                        }
                      ],
                      "type": "BIGINT",
                      "name": "EXPR$0"
                    }
                  ],
                  "id": "13",
                  "inputs": [
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
                      "id": "12",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "connected",
                          "id": "11",
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
  ],
  "id": "15",
  "inputs": [
    {
      "relOp": "LogicalValues",
      "values": [
        {
          "kind": "LITERAL",
          "value": 0,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
        }
      ],
      "id": "14",
      "inputs": []
    }
  ]
}