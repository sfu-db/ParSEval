{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "9",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "LITERAL",
          "value": 0,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
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
            }
          ],
          "id": "7",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "GREATER_THAN",
                "operator": ">",
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
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              "variableset": "[]",
              "id": "6",
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
                          "index": 2,
                          "name": "$2",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "INPUT_REF",
                          "index": 18,
                          "name": "$18",
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
                                    "value": "Australian Grand Prix",
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
                              },
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