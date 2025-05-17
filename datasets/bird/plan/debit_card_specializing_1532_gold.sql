{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "INTEGER"
    },
    {
      "kind": "INPUT_REF",
      "index": 2,
      "name": "$2",
      "type": "FLOAT"
    },
    {
      "kind": "INPUT_REF",
      "index": 1,
      "name": "$1",
      "type": "VARCHAR"
    }
  ],
  "id": "10",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [
        {
          "column": 0,
          "type": "INTEGER"
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
          "name": "EXPR$1"
        }
      ],
      "id": "9",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 6,
              "name": "$6",
              "type": "INTEGER"
            },
            {
              "kind": "INPUT_REF",
              "index": 2,
              "name": "$2",
              "type": "VARCHAR"
            },
            {
              "kind": "DIVIDE",
              "operator": "/",
              "type": "FLOAT",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 11,
                  "name": "$11",
                  "type": "FLOAT"
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
                          "kind": "INPUT_REF",
                          "index": 10,
                          "name": "$10",
                          "type": "INTEGER"
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
                      "kind": "INPUT_REF",
                      "index": 10,
                      "name": "$10",
                      "type": "INTEGER"
                    }
                  ]
                }
              ]
            }
          ],
          "id": "8",
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
                    "index": 6,
                    "name": "$6",
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
                            "index": 0,
                            "name": "$0",
                            "type": "INTEGER"
                          }
                        ],
                        "id": "3",
                        "inputs": [
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
                            "id": "2",
                            "inputs": [
                              {
                                "relOp": "LogicalProject",
                                "project": [
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 0,
                                    "name": "$0",
                                    "type": "INTEGER"
                                  },
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 2,
                                    "name": "$2",
                                    "type": "FLOAT"
                                  }
                                ],
                                "id": "1",
                                "inputs": [
                                  {
                                    "relOp": "LogicalTableScan",
                                    "table": "yearmonth",
                                    "id": "0",
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
                        "index": 0,
                        "name": "$0",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 6,
                        "name": "$6",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "6",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "customers",
                      "id": "4",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "transactions_1k",
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