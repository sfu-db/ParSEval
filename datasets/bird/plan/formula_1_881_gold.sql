{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "DECIMAL",
      "operands": [
        {
          "kind": "TIMES",
          "operator": "*",
          "type": "DECIMAL",
          "operands": [
            {
              "kind": "MINUS",
              "operator": "-",
              "type": "DECIMAL",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 0,
                  "name": "$0",
                  "type": "DECIMAL"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 1,
                  "name": "$1",
                  "type": "DECIMAL"
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
          "type": "DECIMAL",
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
                  "type": "DECIMAL"
                },
                {
                  "kind": "LITERAL",
                  "value": 0.0,
                  "type": "DECIMAL",
                  "nullable": false,
                  "precision": 19
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": "NULL",
              "type": "DECIMAL",
              "nullable": true,
              "precision": 19
            },
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "DECIMAL"
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
              "type": "DECIMAL"
            }
          ],
          "type": "DECIMAL",
          "name": null
        },
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
              "type": "DECIMAL"
            }
          ],
          "type": "DECIMAL",
          "name": null
        }
      ],
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "DECIMAL",
              "operands": [
                {
                  "kind": "CASE",
                  "operator": "CASE",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "EQUALS",
                      "operator": "=",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 10,
                          "name": "$10",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "LITERAL",
                          "value": 853,
                          "type": "INTEGER",
                          "nullable": false,
                          "precision": 10
                        }
                      ]
                    },
                    {
                      "kind": "INPUT_REF",
                      "index": 25,
                      "name": "$25",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "CAST",
                      "operator": "CAST",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "CAST",
                          "operator": "CAST",
                          "type": "VARCHAR",
                          "operands": [
                            {
                              "kind": "LITERAL",
                              "value": 0,
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
              "kind": "CAST",
              "operator": "CAST",
              "type": "DECIMAL",
              "operands": [
                {
                  "kind": "CASE",
                  "operator": "CASE",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "EQUALS",
                      "operator": "=",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 10,
                          "name": "$10",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "LITERAL",
                          "value": 854,
                          "type": "INTEGER",
                          "nullable": false,
                          "precision": 10
                        }
                      ]
                    },
                    {
                      "kind": "INPUT_REF",
                      "index": 25,
                      "name": "$25",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "CAST",
                      "operator": "CAST",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "CAST",
                          "operator": "CAST",
                          "type": "VARCHAR",
                          "operands": [
                            {
                              "kind": "LITERAL",
                              "value": 0,
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
                        "index": 4,
                        "name": "$4",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "Paul",
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
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "di Resta",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
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
                        "index": 11,
                        "name": "$11",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 0,
                        "name": "$0",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "drivers",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "results",
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