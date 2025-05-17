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
    },
    {
      "column": 2,
      "type": "DATE"
    }
  ],
  "aggs": [],
  "id": "10",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 4,
          "name": "$4",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 6,
          "name": "$6",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 21,
          "name": "$21",
          "type": "DATE"
        }
      ],
      "id": "9",
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
                            "type": "VARCHAR"
                          }
                        ],
                        "id": "6",
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
                            "id": "5",
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
                                    "type": "BIGINT"
                                  }
                                ],
                                "id": "4",
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
                                        "name": null
                                      }
                                    ],
                                    "id": "3",
                                    "inputs": [
                                      {
                                        "relOp": "LogicalProject",
                                        "project": [
                                          {
                                            "kind": "INPUT_REF",
                                            "index": 4,
                                            "name": "$4",
                                            "type": "VARCHAR"
                                          },
                                          {
                                            "kind": "INPUT_REF",
                                            "index": 6,
                                            "name": "$6",
                                            "type": "VARCHAR"
                                          }
                                        ],
                                        "id": "2",
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
                                                  "index": 3,
                                                  "name": "$3",
                                                  "type": "VARCHAR"
                                                },
                                                {
                                                  "kind": "LITERAL",
                                                  "value": "Closed",
                                                  "type": "VARCHAR",
                                                  "nullable": false,
                                                  "precision": -1
                                                }
                                              ]
                                            },
                                            "variableset": "[]",
                                            "id": "1",
                                            "inputs": [
                                              {
                                                "relOp": "LogicalTableScan",
                                                "table": "schools",
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
                          }
                        ]
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
                    "kind": "INPUT_REF",
                    "index": 3,
                    "name": "$3",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "Closed",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
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
                        "index": 6,
                        "name": "$6",
                        "type": "VARCHAR"
                      }
                    ]
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "8",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "schools",
              "id": "7",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}