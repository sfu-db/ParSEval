{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 4,
      "name": "$4",
      "type": "VARCHAR"
    }
  ],
  "id": "10",
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
                            "index": 5,
                            "name": "$5",
                            "type": "DATE"
                          }
                        ]
                      }
                    ]
                  }
                ]
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
                    "id": "3",
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
                        "id": "2",
                        "inputs": [
                          {
                            "relOp": "LogicalProject",
                            "project": [
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
                                            "index": 5,
                                            "name": "$5",
                                            "type": "DATE"
                                          }
                                        ]
                                      }
                                    ]
                                  }
                                ]
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 5,
                                "name": "$5",
                                "type": "DATE"
                              }
                            ],
                            "id": "1",
                            "inputs": [
                              {
                                "relOp": "LogicalTableScan",
                                "table": "races",
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
          {
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
                    "value": "%m",
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
                            "index": 5,
                            "name": "$5",
                            "type": "DATE"
                          }
                        ]
                      }
                    ]
                  }
                ]
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
                    "id": "7",
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
                        "id": "6",
                        "inputs": [
                          {
                            "relOp": "LogicalProject",
                            "project": [
                              {
                                "kind": "OTHER_FUNCTION",
                                "operator": "STRFTIME",
                                "type": "VARCHAR",
                                "operands": [
                                  {
                                    "kind": "LITERAL",
                                    "value": "%m",
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
                                            "index": 5,
                                            "name": "$5",
                                            "type": "DATE"
                                          }
                                        ]
                                      }
                                    ]
                                  }
                                ]
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 5,
                                "name": "$5",
                                "type": "DATE"
                              }
                            ],
                            "id": "5",
                            "inputs": [
                              {
                                "relOp": "LogicalTableScan",
                                "table": "races",
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
      "id": "9",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "races",
          "id": "8",
          "inputs": []
        }
      ]
    }
  ]
}