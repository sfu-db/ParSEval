{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "FLOAT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "11",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
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
          "type": "FLOAT"
        }
      ],
      "id": "10",
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
                "index": 2,
                "name": "$2",
                "type": "BIGINT"
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
          "id": "9",
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
                  "name": "DESC"
                },
                {
                  "operator": "COUNT",
                  "distinct": true,
                  "ignoreNulls": false,
                  "operands": [
                    {
                      "column": 2,
                      "type": "VARCHAR"
                    }
                  ],
                  "type": "BIGINT",
                  "name": null
                }
              ],
              "id": "8",
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
                      "index": 3,
                      "name": "$3",
                      "type": "FLOAT"
                    },
                    {
                      "kind": "INPUT_REF",
                      "index": 23,
                      "name": "$23",
                      "type": "VARCHAR"
                    }
                  ],
                  "id": "7",
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
                            "index": 22,
                            "name": "$22",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 23,
                            "name": "$23",
                            "type": "VARCHAR"
                          }
                        ]
                      },
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
                                "index": 6,
                                "name": "$6",
                                "type": "VARCHAR"
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 16,
                                "name": "$16",
                                "type": "VARCHAR"
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
                                    "index": 5,
                                    "name": "$5",
                                    "type": "VARCHAR"
                                  },
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 7,
                                    "name": "$7",
                                    "type": "VARCHAR"
                                  }
                                ]
                              },
                              "id": "2",
                              "inputs": [
                                {
                                  "relOp": "LogicalTableScan",
                                  "table": "expense",
                                  "id": "0",
                                  "inputs": []
                                },
                                {
                                  "relOp": "LogicalTableScan",
                                  "table": "member",
                                  "id": "1",
                                  "inputs": []
                                }
                              ]
                            },
                            {
                              "relOp": "LogicalTableScan",
                              "table": "budget",
                              "id": "3",
                              "inputs": []
                            }
                          ]
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "event",
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
      ]
    }
  ]
}