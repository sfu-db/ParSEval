{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 0,
      "type": "FLOAT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "9",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 2,
          "name": "$2",
          "type": "FLOAT"
        },
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "VARCHAR"
        }
      ],
      "id": "8",
      "inputs": [
        {
          "relOp": "LogicalAggregate",
          "keys": [
            {
              "column": 0,
              "type": "VARCHAR"
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
                  "type": "FLOAT"
                }
              ],
              "type": "FLOAT",
              "name": "EXPR$0"
            }
          ],
          "id": "7",
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
                  "kind": "INPUT_REF",
                  "index": 8,
                  "name": "$8",
                  "type": "VARCHAR"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 3,
                  "name": "$3",
                  "type": "FLOAT"
                }
              ],
              "id": "6",
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
                            "index": 14,
                            "name": "$14",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": "Monaco Grand Prix",
                            "type": "VARCHAR",
                            "nullable": false,
                            "precision": -1
                          }
                        ]
                      },
                      {
                        "kind": "GREATER_THAN_OR_EQUAL",
                        "operator": ">=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 11,
                            "name": "$11",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 1980,
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
                            "kind": "INPUT_REF",
                            "index": 11,
                            "name": "$11",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 2010,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          }
                        ]
                      }
                    ]
                  },
                  "variableset": "[]",
                  "id": "5",
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
                            "index": 10,
                            "name": "$10",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 1,
                            "name": "$1",
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
                                "index": 2,
                                "name": "$2",
                                "type": "INTEGER"
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 5,
                                "name": "$5",
                                "type": "INTEGER"
                              }
                            ]
                          },
                          "id": "2",
                          "inputs": [
                            {
                              "relOp": "LogicalTableScan",
                              "table": "constructorResults",
                              "id": "0",
                              "inputs": []
                            },
                            {
                              "relOp": "LogicalTableScan",
                              "table": "constructors",
                              "id": "1",
                              "inputs": []
                            }
                          ]
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "races",
                          "id": "3",
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