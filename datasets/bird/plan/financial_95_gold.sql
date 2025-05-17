{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 2,
      "type": "INTEGER"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "14",
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
          "kind": "SCALAR_QUERY",
          "operator": "$SCALAR_QUERY",
          "operands": [],
          "query": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "MINUS",
                  "operator": "-",
                  "type": "INTEGER",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 0,
                      "name": "$0",
                      "type": "INTEGER"
                    },
                    {
                      "kind": "INPUT_REF",
                      "index": 1,
                      "name": "$1",
                      "type": "INTEGER"
                    }
                  ]
                }
              ],
              "id": "3",
              "inputs": [
                {
                  "relOp": "LogicalAggregate",
                  "keys": [],
                  "aggs": [
                    {
                      "operator": "MAX",
                      "distinct": false,
                      "ignoreNulls": false,
                      "operands": [
                        {
                          "column": 0,
                          "type": "INTEGER"
                        }
                      ],
                      "type": "INTEGER",
                      "name": null
                    },
                    {
                      "operator": "MIN",
                      "distinct": false,
                      "ignoreNulls": false,
                      "operands": [
                        {
                          "column": 0,
                          "type": "INTEGER"
                        }
                      ],
                      "type": "INTEGER",
                      "name": null
                    }
                  ],
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalProject",
                      "project": [
                        {
                          "kind": "INPUT_REF",
                          "index": 10,
                          "name": "$10",
                          "type": "INTEGER"
                        }
                      ],
                      "id": "1",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "district",
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
        },
        {
          "kind": "INPUT_REF",
          "index": 14,
          "name": "$14",
          "type": "INTEGER"
        }
      ],
      "id": "13",
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
                "index": 4,
                "name": "$4",
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
                    "id": "8",
                    "inputs": [
                      {
                        "relOp": "LogicalSort",
                        "sort": [
                          {
                            "column": 1,
                            "type": "DATE"
                          }
                        ],
                        "dir": [
                          "ASCENDING"
                        ],
                        "offset": 0,
                        "limit": 1,
                        "id": "7",
                        "inputs": [
                          {
                            "relOp": "LogicalProject",
                            "project": [
                              {
                                "kind": "INPUT_REF",
                                "index": 3,
                                "name": "$3",
                                "type": "INTEGER"
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 2,
                                "name": "$2",
                                "type": "DATE"
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
                                      "kind": "INPUT_REF",
                                      "index": 1,
                                      "name": "$1",
                                      "type": "VARCHAR"
                                    },
                                    {
                                      "kind": "LITERAL",
                                      "value": "F",
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
                                    "relOp": "LogicalTableScan",
                                    "table": "client",
                                    "id": "4",
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
          },
          "variableset": "[]",
          "id": "12",
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
              "id": "11",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "account",
                  "id": "9",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "district",
                  "id": "10",
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