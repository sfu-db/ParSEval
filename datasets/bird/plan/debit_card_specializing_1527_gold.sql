{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "FLOAT",
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
                  "index": 0,
                  "name": "$0",
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
              "index": 0,
              "name": "$0",
              "type": "FLOAT"
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
                      "kind": "OTHER_FUNCTION",
                      "operator": "SUBSTRING",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 1,
                          "name": "$1",
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
                          "value": 4,
                          "type": "INTEGER",
                          "nullable": false,
                          "precision": 10
                        }
                      ]
                    },
                    {
                      "kind": "LITERAL",
                      "value": "2012",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 2,
                  "name": "$2",
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
                      "kind": "OTHER_FUNCTION",
                      "operator": "SUBSTRING",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 1,
                          "name": "$1",
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
                          "value": 4,
                          "type": "INTEGER",
                          "nullable": false,
                          "precision": 10
                        }
                      ]
                    },
                    {
                      "kind": "LITERAL",
                      "value": "2013",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 2,
                  "name": "$2",
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
                    "kind": "SCALAR_QUERY",
                    "operator": "$SCALAR_QUERY",
                    "operands": [],
                    "query": [
                      {
                        "relOp": "LogicalProject",
                        "project": [
                          {
                            "kind": "INPUT_REF",
                            "index": 3,
                            "name": "$3",
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
                                      "index": 1,
                                      "name": "$1",
                                      "type": "DATE"
                                    },
                                    {
                                      "kind": "CAST",
                                      "operator": "CAST",
                                      "type": "DATE",
                                      "operands": [
                                        {
                                          "kind": "LITERAL",
                                          "value": "2012-08-25",
                                          "type": "CHAR",
                                          "nullable": false,
                                          "precision": 10
                                        }
                                      ]
                                    }
                                  ]
                                },
                                {
                                  "kind": "EQUALS",
                                  "operator": "=",
                                  "type": "BOOLEAN",
                                  "operands": [
                                    {
                                      "kind": "CAST",
                                      "operator": "CAST",
                                      "type": "DOUBLE",
                                      "operands": [
                                        {
                                          "kind": "INPUT_REF",
                                          "index": 8,
                                          "name": "$8",
                                          "type": "FLOAT"
                                        }
                                      ]
                                    },
                                    {
                                      "kind": "LITERAL",
                                      "value": 634.8,
                                      "type": "DOUBLE",
                                      "nullable": false,
                                      "precision": 15
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
                                      "index": 5,
                                      "name": "$5",
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
                                    "table": "transactions_1k",
                                    "id": "0",
                                    "inputs": []
                                  },
                                  {
                                    "relOp": "LogicalTableScan",
                                    "table": "gasstations",
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
              },
              "variableset": "[]",
              "id": "6",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "yearmonth",
                  "id": "5",
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