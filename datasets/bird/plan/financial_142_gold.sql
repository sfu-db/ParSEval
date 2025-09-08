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
  "id": "9",
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
            "type": "INTEGER"
          },
          {
            "kind": "LITERAL",
            "value": 10000,
            "type": "INTEGER",
            "nullable": false,
            "precision": 10
          }
        ]
      },
      "variableset": "[]",
      "id": "8",
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
              "operator": "SUM",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 1,
                  "type": "INTEGER"
                }
              ],
              "type": "INTEGER",
              "name": null
            }
          ],
          "id": "7",
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
                  "index": 25,
                  "name": "$25",
                  "type": "INTEGER"
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
                        "kind": "OTHER_FUNCTION",
                        "operator": "STRFTIME",
                        "type": "VARCHAR",
                        "operands": [
                          {
                            "kind": "LITERAL",
                            "value": "%Y",
                            "type": "CHAR",
                            "nullable": false,
                            "precision": 2
                          },
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "VARCHAR",
                            "operands": [
                              {
                                "kind": "CAST",
                                "operator": "CAST",
                                "type": "TIMESTAMP",
                                "operands": [
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 22,
                                    "name": "$22",
                                    "type": "DATE"
                                  }
                                ]
                              }
                            ]
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "1997",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
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
                            "index": 0,
                            "name": "$0",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 21,
                            "name": "$21",
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
                                "index": 1,
                                "name": "$1",
                                "type": "INTEGER"
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 4,
                                "name": "$4",
                                "type": "INTEGER"
                              }
                            ]
                          },
                          "id": "2",
                          "inputs": [
                            {
                              "relOp": "LogicalTableScan",
                              "table": "account",
                              "id": "0",
                              "inputs": []
                            },
                            {
                              "relOp": "LogicalTableScan",
                              "table": "district",
                              "id": "1",
                              "inputs": []
                            }
                          ]
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "trans",
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