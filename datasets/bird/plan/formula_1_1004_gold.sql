{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 0,
      "type": "BIGINT"
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
          "index": 1,
          "name": "$1",
          "type": "BIGINT"
        }
      ],
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
              "operator": "COUNT",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 0,
                  "type": "INTEGER"
                }
              ],
              "type": "BIGINT",
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
                  "index": 2,
                  "name": "$2",
                  "type": "INTEGER"
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
                            "index": 26,
                            "name": "$26",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 3,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
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
                            "index": 22,
                            "name": "$22",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": "Canadian Grand Prix",
                            "type": "VARCHAR",
                            "nullable": false,
                            "precision": -1
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
                            "index": 17,
                            "name": "$17",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 26,
                            "name": "$26",
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
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "status",
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